// Left-drawer governance authoring: a 3-step wizard (pick Metric → Policy →
// Threshold) that writes a Policy node + Threshold node + the 3 governance edges
// via POST /api/governance. An optional "prefill from a document" box LLM-parses
// pasted/uploaded text into the policy + threshold fields (POST
// /api/governance/extract). All state is local to this component (never persisted
// half-filled); only the drawer-open flag lives in the store.

import { useMemo, useState } from "react"
import {
  Check,
  ChevronLeft,
  ChevronRight,
  Loader2,
  Sparkles,
  Upload,
  X,
} from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Separator } from "@/components/ui/separator"
import { Textarea } from "@/components/ui/textarea"
import {
  api,
  type GraphNode,
  type PolicyDraft,
  type ThresholdDraft,
} from "@/lib/api"
import { useStore } from "@/store"

// --- Enum option vocabularies (mirror the Pydantic model enums) -------------
const POLICY_TYPES = [
  "alerting",
  "action_guardrail",
  "approval",
  "escalation",
  "interpretation",
  "access",
  "data_quality",
]
const OPERATORS = ["lt", "lte", "gt", "gte", "eq", "neq", "between", "outside"]
const SEVERITIES = ["critical", "high", "medium", "low", "info", "blocking"]
const THRESHOLD_TYPES = [
  "percentile",
  "static",
  "target",
  "warning",
  "critical",
  "seasonal",
  "anomaly",
  "sla",
  "budget",
]
const DIRECTIONS = ["higher_is_better", "lower_is_better", "target_is_best"]

type Step = 1 | 2 | 3

/** Parse a number input → number | undefined (blank / NaN ⇒ undefined). */
function num(v: string): number | undefined {
  if (v.trim() === "") return undefined
  const n = Number(v)
  return Number.isNaN(n) ? undefined : n
}

/** Drop undefined / null / "" keys so a prefill never clobbers with blanks. */
function clean<T extends object>(draft: T | undefined): Partial<T> {
  const out: Record<string, unknown> = {}
  for (const [k, v] of Object.entries(draft ?? {})) {
    if (v !== undefined && v !== null && v !== "") out[k] = v
  }
  return out as Partial<T>
}

// --- Small labeled field primitives -----------------------------------------
function Labeled({
  label,
  hint,
  children,
}: {
  label: string
  hint?: string
  children: React.ReactNode
}) {
  return (
    <div className="flex flex-col gap-1 text-xs">
      <span className="font-medium text-muted-foreground">{label}</span>
      {children}
      {hint ? <span className="text-[10px] text-muted-foreground">{hint}</span> : null}
    </div>
  )
}

function TextField({
  label,
  value,
  onChange,
  placeholder,
}: {
  label: string
  value: string | undefined
  onChange: (v: string | undefined) => void
  placeholder?: string
}) {
  return (
    <Labeled label={label}>
      <Input
        value={value ?? ""}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value || undefined)}
        className="h-8 text-xs"
      />
    </Labeled>
  )
}

function NumField({
  label,
  value,
  onChange,
  hint,
}: {
  label: string
  value: number | undefined
  onChange: (v: number | undefined) => void
  hint?: string
}) {
  return (
    <Labeled label={label} hint={hint}>
      <Input
        type="number"
        inputMode="decimal"
        value={value ?? ""}
        onChange={(e) => onChange(num(e.target.value))}
        className="h-8 text-xs"
      />
    </Labeled>
  )
}

function SelectField({
  label,
  value,
  onChange,
  options,
  placeholder,
}: {
  label: string
  value: string | undefined
  onChange: (v: string) => void
  options: string[]
  placeholder?: string
}) {
  return (
    <Labeled label={label}>
      <Select value={value ?? ""} onValueChange={onChange}>
        <SelectTrigger size="sm" className="w-full text-xs">
          <SelectValue placeholder={placeholder ?? "—"} />
        </SelectTrigger>
        <SelectContent>
          {options.map((o) => (
            <SelectItem key={o} value={o} className="text-xs">
              {o}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </Labeled>
  )
}

/** The drawer shell + the wizard. Rendered inside App's left `<aside>`. */
export function GovernancePanel() {
  const nodes = useStore((s) => s.nodes)
  const setGovernanceOpen = useStore((s) => s.setGovernanceOpen)
  const loadGraph = useStore((s) => s.loadGraph)
  const loadStatus = useStore((s) => s.loadStatus)
  const locate = useStore((s) => s.locate)

  const [step, setStep] = useState<Step>(1)
  const [metricUid, setMetricUid] = useState<string | null>(null)
  const [policy, setPolicy] = useState<PolicyDraft>({})
  const [threshold, setThreshold] = useState<ThresholdDraft>({})
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<{ kind: "ok" | "err"; text: string } | null>(null)

  const patchPolicy = (p: Partial<PolicyDraft>) =>
    setPolicy((prev) => ({ ...prev, ...p }))
  const patchThreshold = (p: Partial<ThresholdDraft>) =>
    setThreshold((prev) => ({ ...prev, ...p }))

  // --- Step 1: metric picker ------------------------------------------------
  const [query, setQuery] = useState("")
  const metrics = useMemo(
    () => nodes.filter((n) => n.label === "Metric"),
    [nodes]
  )
  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase()
    const list = needle
      ? metrics.filter(
          (m) =>
            (m.title ?? "").toLowerCase().includes(needle) ||
            m.id.toLowerCase().includes(needle)
        )
      : metrics
    return list.slice(0, 80)
  }, [metrics, query])
  const selectedNode = useMemo(
    () =>
      metrics.find(
        (m) => ((m.props?.metric_uid as string) ?? m.id) === metricUid
      ) ?? null,
    [metrics, metricUid]
  )

  function pickMetric(n: GraphNode) {
    const uid = (n.props?.metric_uid as string) ?? n.id
    const unitFamily = n.props?.unit_family as string | undefined
    const dir = n.props?.default_direction as string | undefined
    const direction = dir && dir !== "neutral" ? dir : undefined
    if (uid !== metricUid) {
      // Switching metrics: start the policy + threshold drafts fresh so a prior
      // metric's band values never carry over to a different metric.
      setMetricUid(uid)
      setThreshold({ unit: unitFamily, direction })
      setPolicy({ policy_name: `${n.title || uid} guardrail` })
      return
    }
    patchThreshold({
      unit: threshold.unit ?? unitFamily,
      direction: threshold.direction ?? direction,
    })
    if (!policy.policy_name) {
      patchPolicy({ policy_name: `${n.title || uid} guardrail` })
    }
  }

  // --- Document prefill (extract) -------------------------------------------
  const [docText, setDocText] = useState("")
  const [extracting, setExtracting] = useState(false)

  async function onParse() {
    if (!docText.trim()) return
    setExtracting(true)
    setMsg(null)
    try {
      const draft = await api.extractGovernance({
        text: docText,
        metric_uid: metricUid ?? undefined,
        metric_name: selectedNode?.title,
      })
      if (draft.error) {
        setMsg({ kind: "err", text: `Extraction: ${draft.error}` })
      } else {
        patchPolicy(clean(draft.policy))
        patchThreshold(clean(draft.threshold))
        setMsg({
          kind: "ok",
          text: "Prefilled from document — review the fields below.",
        })
      }
    } catch (e) {
      setMsg({ kind: "err", text: e instanceof Error ? e.message : String(e) })
    } finally {
      setExtracting(false)
    }
  }

  async function onFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    if (!file) return
    try {
      setDocText(await file.text())
    } catch {
      setMsg({ kind: "err", text: "Could not read that file." })
    }
    e.target.value = "" // allow re-selecting the same file
  }

  // --- Create ---------------------------------------------------------------
  async function onCreate() {
    if (!metricUid) return
    setBusy(true)
    setMsg(null)
    try {
      const res = await api.createGovernance({ metric_uid: metricUid, policy, threshold })
      await loadGraph()
      await loadStatus()
      setMsg({
        kind: res.warning ? "err" : "ok",
        text: res.warning
          ? `Saved nodes, but: ${res.warning}`
          : `Created policy on ${metricUid}. Add another policy to the same metric below, or use Back to pick a new metric.`,
      })
      locate({ kind: "node", id: res.threshold.key })
      // A metric can carry several policies, all enforcing the one shared
      // Threshold band-set. Keep the metric + threshold and clear only the
      // policy, returning to the Policy step so the next policy attaches to the
      // same threshold (re-submitting re-sends identical threshold values —
      // idempotent, no data loss). Back from step 2 returns to the metric picker.
      setPolicy({})
      setStep(2)
      setDocText("")
    } catch (e) {
      setMsg({ kind: "err", text: e instanceof Error ? e.message : String(e) })
    } finally {
      setBusy(false)
    }
  }

  const stepLabels = ["Metric", "Policy", "Threshold"]

  return (
    <div className="flex h-full flex-col">
      {/* Header */}
      <div className="flex items-center justify-between gap-2 border-b border-border px-3 py-2">
        <div className="flex items-center gap-2">
          <span className="text-base leading-none text-[#ef6f6f]">§</span>
          <span className="text-sm font-semibold">Add policy &amp; threshold</span>
        </div>
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label="Close governance"
          title="Close"
          onClick={() => setGovernanceOpen(false)}
        >
          <X />
        </Button>
      </div>

      {/* Step indicator */}
      <div className="flex items-center gap-1.5 border-b border-border px-3 py-2">
        {stepLabels.map((lbl, i) => {
          const n = (i + 1) as Step
          const active = n === step
          const done = n < step
          return (
            <div key={lbl} className="flex items-center gap-1.5">
              <Badge
                variant={active ? "default" : done ? "secondary" : "outline"}
                className="gap-1 text-[10px]"
              >
                {done ? <Check className="size-3" /> : <span>{n}</span>}
                {lbl}
              </Badge>
              {i < 2 ? <ChevronRight className="size-3 text-muted-foreground" /> : null}
            </div>
          )
        })}
      </div>

      {/* Body */}
      <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto px-3 py-3">
        {msg ? (
          <div
            className={
              "rounded-md px-2.5 py-2 text-xs " +
              (msg.kind === "ok"
                ? "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400"
                : "bg-amber-500/15 text-amber-600 dark:text-amber-400")
            }
          >
            {msg.text}
          </div>
        ) : null}

        {step === 1 ? (
          <div className="flex flex-col gap-2">
            <span className="text-xs font-medium text-muted-foreground">
              Select the metric this policy applies to
            </span>
            <Input
              autoFocus
              value={query}
              placeholder="Search metrics…"
              onChange={(e) => setQuery(e.target.value)}
              className="h-8 text-xs"
            />
            <div className="max-h-[55vh] overflow-y-auto rounded-md border border-border">
              {filtered.length === 0 ? (
                <div className="px-3 py-4 text-center text-xs text-muted-foreground">
                  {metrics.length === 0
                    ? "No metrics loaded yet."
                    : "No metric matches that search."}
                </div>
              ) : (
                filtered.map((m) => {
                  const uid = (m.props?.metric_uid as string) ?? m.id
                  const selected = uid === metricUid
                  return (
                    <button
                      key={m.id}
                      type="button"
                      onClick={() => pickMetric(m)}
                      className={
                        "flex w-full flex-col items-start gap-0.5 border-b border-border/60 px-2.5 py-1.5 text-left text-xs last:border-b-0 hover:bg-accent " +
                        (selected ? "bg-accent" : "")
                      }
                    >
                      <span className="font-medium">{m.title || uid}</span>
                      <span className="font-mono text-[10px] text-muted-foreground">
                        {uid}
                      </span>
                    </button>
                  )
                })
              )}
            </div>
            <span className="text-[10px] text-muted-foreground">
              {metrics.length} metric(s) · showing {filtered.length}
            </span>
          </div>
        ) : null}

        {step === 2 ? (
          <div className="flex flex-col gap-3">
            {/* Selected metric chip */}
            <div className="rounded-md bg-muted/40 px-2.5 py-1.5 text-xs">
              <span className="text-muted-foreground">Governing </span>
              <span className="font-mono">{metricUid}</span>
            </div>

            {/* Document prefill */}
            <div className="flex flex-col gap-2 rounded-md border border-dashed border-border p-2.5">
              <div className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
                <Sparkles className="size-3.5" /> Prefill from a document (optional)
              </div>
              <Textarea
                value={docText}
                placeholder="Paste a policy doc, JSON, or notes… the model fills the policy + threshold fields below (industry standards from its own knowledge)."
                onChange={(e) => setDocText(e.target.value)}
                className="min-h-16 text-xs"
              />
              <div className="flex items-center gap-1.5">
                <Button
                  size="xs"
                  variant="outline"
                  disabled={!docText.trim() || extracting}
                  onClick={() => void onParse()}
                >
                  {extracting ? (
                    <Loader2 className="size-3 animate-spin" />
                  ) : (
                    <Sparkles className="size-3" />
                  )}
                  Parse with AI
                </Button>
                <Button size="xs" variant="ghost" asChild>
                  <label className="cursor-pointer">
                    <Upload className="size-3" /> Upload .txt/.md/.json/.csv
                    <input
                      type="file"
                      accept=".txt,.md,.json,.csv,text/plain,application/json"
                      className="hidden"
                      onChange={(e) => void onFile(e)}
                    />
                  </label>
                </Button>
              </div>
            </div>

            <Separator />

            <span className="text-xs font-semibold">Policy — the rule to obey</span>
            <TextField
              label="Name"
              value={policy.policy_name}
              onChange={(v) => patchPolicy({ policy_name: v })}
            />
            <Labeled label="Description">
              <Textarea
                value={policy.description ?? ""}
                onChange={(e) =>
                  patchPolicy({ description: e.target.value || undefined })
                }
                className="min-h-14 text-xs"
              />
            </Labeled>
            <div className="grid grid-cols-2 gap-2">
              <SelectField
                label="Type"
                value={policy.policy_type}
                onChange={(v) => patchPolicy({ policy_type: v })}
                options={POLICY_TYPES}
              />
              <SelectField
                label="Severity"
                value={policy.severity}
                onChange={(v) => patchPolicy({ severity: v })}
                options={SEVERITIES}
              />
              <SelectField
                label="Breach operator"
                value={policy.condition_operator}
                onChange={(v) => patchPolicy({ condition_operator: v })}
                options={OPERATORS}
              />
              <NumField
                label="Breach value"
                value={policy.condition_value}
                onChange={(v) => patchPolicy({ condition_value: v })}
              />
            </div>
            <label className="flex items-center gap-2 text-xs">
              <input
                type="checkbox"
                checked={policy.approval_required ?? false}
                onChange={(e) =>
                  patchPolicy({ approval_required: e.target.checked })
                }
              />
              <span className="text-muted-foreground">Approval required to change</span>
            </label>
          </div>
        ) : null}

        {step === 3 ? (
          <div className="flex flex-col gap-3">
            <span className="text-xs font-semibold">Threshold — the breach lines</span>
            <div className="grid grid-cols-2 gap-2">
              <SelectField
                label="Type"
                value={threshold.threshold_type}
                onChange={(v) => patchThreshold({ threshold_type: v })}
                options={THRESHOLD_TYPES}
              />
              <SelectField
                label="Direction"
                value={threshold.direction}
                onChange={(v) => patchThreshold({ direction: v })}
                options={DIRECTIONS}
              />
              <TextField
                label="Unit"
                value={threshold.unit}
                onChange={(v) => patchThreshold({ unit: v })}
                placeholder="ratio · percent · currency"
              />
              <SelectField
                label="Severity"
                value={threshold.severity}
                onChange={(v) => patchThreshold({ severity: v })}
                options={SEVERITIES}
              />
            </div>

            <Separator />
            <span className="text-xs font-semibold">
              Company percentile bands
              <span className="ml-1 font-normal text-muted-foreground">
                (your own distribution)
              </span>
            </span>
            <div className="grid grid-cols-4 gap-2">
              <NumField
                label="p50"
                value={threshold.p50_val}
                onChange={(v) => patchThreshold({ p50_val: v })}
              />
              <NumField
                label="p75"
                value={threshold.p75_val}
                onChange={(v) => patchThreshold({ p75_val: v })}
              />
              <NumField
                label="p85"
                value={threshold.p85_val}
                onChange={(v) => patchThreshold({ p85_val: v })}
              />
              <NumField
                label="p95"
                value={threshold.p95_val}
                onChange={(v) => patchThreshold({ p95_val: v })}
              />
            </div>
            <span className="text-[10px] text-muted-foreground">
              For lower-is-better metrics (CPC, CPA) the good tail is low, so the
              ladder descends (p95 &lt; p50).
            </span>

            <Separator />
            <span className="text-xs font-semibold">Industry benchmark</span>
            <div className="grid grid-cols-3 gap-2">
              <NumField
                label="Min"
                value={threshold.industry_min_val}
                onChange={(v) => patchThreshold({ industry_min_val: v })}
              />
              <NumField
                label="Standard"
                value={threshold.industry_standard_val}
                onChange={(v) => patchThreshold({ industry_standard_val: v })}
              />
              <NumField
                label="Max"
                value={threshold.industry_max_val}
                onChange={(v) => patchThreshold({ industry_max_val: v })}
              />
            </div>
            <div className="grid grid-cols-2 gap-2">
              <TextField
                label="Source"
                value={threshold.industry_source}
                onChange={(v) => patchThreshold({ industry_source: v })}
                placeholder="WordStream 2024"
              />
              <TextField
                label="As of"
                value={threshold.industry_as_of}
                onChange={(v) => patchThreshold({ industry_as_of: v })}
                placeholder="2024-01-01"
              />
            </div>

            <Separator />
            <div className="grid grid-cols-2 gap-2">
              <NumField
                label="Current value"
                value={threshold.current_val}
                onChange={(v) => patchThreshold({ current_val: v })}
              />
              <NumField
                label="Target"
                value={threshold.target_value_num}
                onChange={(v) => patchThreshold({ target_value_num: v })}
              />
            </div>
            <Labeled label="Explanation">
              <Textarea
                value={threshold.explanation ?? ""}
                onChange={(e) =>
                  patchThreshold({ explanation: e.target.value || undefined })
                }
                className="min-h-14 text-xs"
              />
            </Labeled>
          </div>
        ) : null}
      </div>

      {/* Footer nav */}
      <div className="flex items-center justify-between gap-2 border-t border-border px-3 py-2">
        <Button
          size="xs"
          variant="ghost"
          disabled={step === 1 || busy}
          onClick={() => setStep((s) => (s > 1 ? ((s - 1) as Step) : s))}
        >
          <ChevronLeft className="size-3" /> Back
        </Button>
        {step < 3 ? (
          <Button
            size="xs"
            disabled={step === 1 && !metricUid}
            onClick={() => setStep((s) => (s < 3 ? ((s + 1) as Step) : s))}
          >
            Next <ChevronRight className="size-3" />
          </Button>
        ) : (
          <Button size="xs" disabled={!metricUid || busy} onClick={() => void onCreate()}>
            {busy ? <Loader2 className="size-3 animate-spin" /> : <Check className="size-3" />}
            Create
          </Button>
        )}
      </div>
    </div>
  )
}
