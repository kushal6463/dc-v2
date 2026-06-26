// Ingestion progress bar — hidden when no run is in flight.

import { useStore } from "@/store"

export function ProgressBar() {
  const progress = useStore((s) => s.progress)
  const running = useStore((s) => s.running)

  if (!running && !progress) {
    return null
  }

  const done = progress?.done ?? 0
  const total = progress?.total ?? 0
  const pct = total > 0 ? Math.min(100, Math.round((done / total) * 100)) : 0

  return (
    <div className="flex items-center gap-3 border-b border-border bg-muted/40 px-4 py-2">
      <div className="flex-1">
        <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
          <div
            className="h-full rounded-full bg-primary transition-all duration-300"
            style={{ width: `${pct}%` }}
          />
        </div>
      </div>
      <div className="min-w-0 text-xs text-muted-foreground">
        {total > 0 ? (
          <span>
            {done}/{total}
            {progress?.dashboard ? ` — ${progress.dashboard}` : ""}
          </span>
        ) : (
          <span>starting…</span>
        )}
      </div>
    </div>
  )
}
