// Cmd+K / Ctrl+K command palette over the loaded graph nodes. Substring-filters
// listNodesForSearch() by id / label / scope; Enter or click locates the node
// (store.locate) and closes. Registers the global hotkey listener (mount once).

import { useEffect, useMemo, useRef, useState } from "react"
import { Search } from "lucide-react"

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { cn } from "@/lib/utils"
import { useStore, type NodeSearchEntry } from "@/store"

// Score a node row against the lowercased query. Returns null for non-matches so
// the caller can drop them; lower score = better (id/label prefix beats a deep
// substring hit) so the most relevant rows float to the top.
function matchScore(entry: NodeSearchEntry, q: string): number | null {
  const id = entry.id.toLowerCase()
  const label = entry.label.toLowerCase()
  const scope = (entry.scope ?? "").toLowerCase()
  if (id === q || label === q) return 0
  if (id.startsWith(q) || label.startsWith(q)) return 1
  const li = label.indexOf(q)
  if (li >= 0) return 2 + li / 100
  const ii = id.indexOf(q)
  if (ii >= 0) return 3 + ii / 100
  if (scope.includes(q)) return 4
  return null
}

const MAX_RESULTS = 50

export function CommandSearch() {
  const searchOpen = useStore((s) => s.searchOpen)
  const setSearchOpen = useStore((s) => s.setSearchOpen)
  const locate = useStore((s) => s.locate)
  const listNodesForSearch = useStore((s) => s.listNodesForSearch)
  // Re-derive the palette rows whenever the underlying graph changes.
  const nodes = useStore((s) => s.nodes)

  const [query, setQuery] = useState("")
  const [active, setActive] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)

  // Global Cmd+K / Ctrl+K toggle (mounted once with this component).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault()
        setSearchOpen(!useStore.getState().searchOpen)
      }
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [setSearchOpen])

  // Reset the query/highlight each time the palette opens.
  useEffect(() => {
    if (searchOpen) {
      setQuery("")
      setActive(0)
    }
  }, [searchOpen])

  const entries = useMemo(
    () => listNodesForSearch(),
    // nodes drives the underlying data; listNodesForSearch is a stable selector.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [listNodesForSearch, nodes]
  )

  const results = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return entries.slice(0, MAX_RESULTS)
    return entries
      .map((e) => ({ e, s: matchScore(e, q) }))
      .filter((r): r is { e: NodeSearchEntry; s: number } => r.s !== null)
      .sort((a, b) => a.s - b.s || a.e.label.localeCompare(b.e.label))
      .slice(0, MAX_RESULTS)
      .map((r) => r.e)
  }, [entries, query])

  // Keep the active index in range as results change.
  useEffect(() => {
    setActive((i) => (i >= results.length ? 0 : i))
  }, [results.length])

  const choose = (entry: NodeSearchEntry | undefined) => {
    if (!entry) return
    locate({ kind: "node", id: entry.id })
  }

  const onInputKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown") {
      e.preventDefault()
      setActive((i) => Math.min(i + 1, results.length - 1))
    } else if (e.key === "ArrowUp") {
      e.preventDefault()
      setActive((i) => Math.max(i - 1, 0))
    } else if (e.key === "Enter") {
      e.preventDefault()
      choose(results[active])
    }
  }

  return (
    <Dialog open={searchOpen} onOpenChange={setSearchOpen}>
      <DialogContent
        showCloseButton={false}
        onOpenAutoFocus={(e) => {
          e.preventDefault()
          inputRef.current?.focus()
        }}
        className="top-[18%] max-w-lg translate-y-0 gap-0 rounded-2xl p-0 sm:max-w-lg"
      >
        <DialogHeader className="sr-only">
          <DialogTitle>Search nodes</DialogTitle>
          <DialogDescription>
            Find a node by id, label or scope and jump to it on the canvas.
          </DialogDescription>
        </DialogHeader>

        <div className="flex items-center gap-2 border-b border-border px-3 py-2">
          <Search className="size-4 shrink-0 text-muted-foreground" />
          <Input
            ref={inputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={onInputKeyDown}
            placeholder="Search nodes by id, label or scope…"
            className="h-8 rounded-none border-0 bg-transparent px-0 focus-visible:border-0 focus-visible:ring-0"
            aria-label="Search nodes"
          />
        </div>

        <div className="max-h-[50vh] overflow-y-auto py-1">
          {results.length === 0 ? (
            <div className="px-3 py-6 text-center text-xs text-muted-foreground">
              No matching nodes.
            </div>
          ) : (
            results.map((entry, i) => (
              <button
                key={entry.id}
                type="button"
                onClick={() => choose(entry)}
                onMouseEnter={() => setActive(i)}
                className={cn(
                  "flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs transition-colors",
                  i === active
                    ? "bg-accent text-foreground"
                    : "text-muted-foreground hover:bg-accent/60"
                )}
              >
                <span className="min-w-0 flex-1 truncate text-foreground">
                  {entry.label}
                </span>
                {entry.kind && (
                  <span className="shrink-0 rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
                    {entry.kind}
                  </span>
                )}
                {entry.scope && (
                  <span className="shrink-0 truncate font-mono text-[10px] text-muted-foreground">
                    {entry.scope}
                  </span>
                )}
              </button>
            ))
          )}
        </div>

        <div className="flex items-center justify-between border-t border-border px-3 py-1.5 text-[10px] text-muted-foreground">
          <span>{results.length} match{results.length === 1 ? "" : "es"}</span>
          <span className="font-mono">↑↓ navigate · ↵ open · esc close</span>
        </div>
      </DialogContent>
    </Dialog>
  )
}
