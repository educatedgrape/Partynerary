'use client'

const STAGES = [
  { id: 'feed', label: 'Discover', icon: '◎' },
  { id: 'date', label: 'Date consensus', icon: '◷' },
  { id: 'scout', label: 'Scout shortlist', icon: '❖' },
  { id: 'inventory', label: 'Atlas inventory', icon: '⌖' },
  { id: 'graph', label: 'Itinerary graph', icon: '⬡' },
  { id: 'manage', label: 'Manage itinerary', icon: '⬢' },
  { id: 'authority', label: 'Authority', icon: '⚿' },
  { id: 'receipt', label: 'Receipt', icon: '≡' },
]

export function stageIndex(state) {
  if (!state) return 0
  if (state.booked) return 7
  if (state.synthesis || state.decision != null) return 6
  if (state.chosen_graph || state.graph || state.selected_card != null) return 5
  if (state.cards && state.cards.length > 0) return 3
  if (state.date?.agreed || state.agreed_date) return 2
  if (state.members && state.members.length > 0) return 1
  return 0
}

export default function Rail({ state }) {
  const at = stageIndex(state)
  return (
    <nav className="flex flex-col gap-1">
      {STAGES.map((s, i) => {
        const active = i === at
        const done = i < at
        return (
          <a key={s.id} href={`#panel-${s.id}`}
             className={`group flex items-center gap-3 rounded px-3 py-2
                         transition-all duration-200
               ${active
                 ? 'border border-brand-500/30 bg-brand-500/10 text-brand-400 shadow-glow'
                 : 'border border-transparent text-ink3 hover:bg-white/5 hover:text-ink'}`}>
            <span className={`grid h-5 w-5 shrink-0 place-items-center rounded-sm
                              font-pixel text-[11px]
              ${active ? 'text-brand-400'
                       : done ? 'text-ok' : 'text-ink3/70'}`}>
              {done ? '✓' : s.icon}
            </span>
            <span className="font-pixel text-[11px] uppercase tracking-[0.1em]">
              {s.label}
            </span>
            {active && (
              <span className="ml-auto h-1.5 w-1.5 rounded-full bg-brand-400
                               animate-pulseSoft" />
            )}
          </a>
        )
      })}
    </nav>
  )
}
