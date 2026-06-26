// Live scrolling list of activity strings (agent_action / node_written / etc).

import { useStore } from "@/store"

export function ActivityFeed() {
  const activity = useStore((s) => s.activity)

  if (activity.length === 0) {
    return (
      <div className="p-4 text-sm text-muted-foreground">
        No activity yet. Start an ingest to see live events.
      </div>
    )
  }

  return (
    <ul className="flex flex-col gap-1 p-3 font-mono text-xs">
      {activity.map((line, i) => (
        <li
          key={`${i}-${line}`}
          className="rounded border border-border/50 bg-muted/30 px-2 py-1 break-words text-foreground/90"
        >
          {line}
        </li>
      ))}
    </ul>
  )
}
