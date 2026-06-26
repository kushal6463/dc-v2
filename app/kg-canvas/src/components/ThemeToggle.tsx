// Visible theme switcher: cycles system → light → dark. The icon reflects the
// chosen mode (Monitor / Sun / Moon); the "d" hotkey (see theme-provider) still
// flips dark/light independently.

import { Monitor, Moon, Sun } from "lucide-react"

import { useTheme } from "@/components/theme-provider"
import { Button } from "@/components/ui/button"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"

const ORDER = ["system", "light", "dark"] as const

export function ThemeToggle() {
  const { theme, setTheme } = useTheme()

  const next = ORDER[(ORDER.indexOf(theme) + 1) % ORDER.length]
  const Icon = theme === "system" ? Monitor : theme === "light" ? Sun : Moon

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label={`Theme: ${theme}. Switch to ${next}.`}
          onClick={() => setTheme(next)}
        >
          <Icon />
        </Button>
      </TooltipTrigger>
      <TooltipContent>
        Theme: <span className="font-medium capitalize">{theme}</span> · click for {next} · press D to toggle
      </TooltipContent>
    </Tooltip>
  )
}
