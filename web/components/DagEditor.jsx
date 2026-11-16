'use client'

import { memo, useEffect, useRef, useState } from 'react'

/* ── Route strip — a lightweight visualization of the current Option B
 *  path: Origin → Stopover → Destination → Home.
 *
 *  Pure flexbox HTML (no SVG, no coordinates, no layout math), so it is
 *  trivially cheap to render even under 1-second state polling.
 *
 *  Legs come from the backend's as_ui_dict shape: outbound/inbound are
 *  single dicts holding merged flight_numbers/segments for legs 1+2 (out)
 *  and leg 3 (home). normalizeLegs() tolerates that dict form as well as
 *  an array of legs, so swaps always show the newly searched flights.
 *
 *  The stopover and destination chips are clickable: they open a panel
 *  of swap candidates. Stopover candidates are *validated paths* — the
 *  agent already found real flights through each one, so swapping always
 *  returns a new path (instantly, from the cached search results). */

function normalizeLegs(v) {
  if (Array.isArray(v)) return v
  if (v && typeof v === 'object') return [v]
  return []
}

function Arrow({ label }) {
  return (
    <div className="flex min-w-[54px] flex-col items-center px-1">
      <span className="whitespace-nowrap font-mono text-[9px] text-zinc-500">
        {label || '\u00A0'}
      </span>
      <span className="select-none text-xs leading-none text-zinc-600">
        ──▸
      </span>
    </div>
  )
}

const CHIP_TONES = {
  origin: 'border-cyan-400/40 text-cyan-300',
  stop: 'border-amber-400/60 text-amber-300',
  dest: 'border-emerald-400/60 text-emerald-300',
  home: 'border-zinc-600 text-zinc-400',
}

function Chip({ code, sub, tag, tone, onClick, active }) {
  return (
    <button
      type="button"
      disabled={!onClick}
      onClick={onClick}
      className={`rounded-lg border bg-zinc-900/70 px-2.5 py-1.5 text-center transition
        ${CHIP_TONES[tone]} ${active ? 'bg-zinc-800 ring-2 ring-amber-400/60' : ''}
        ${onClick ? 'cursor-pointer hover:bg-zinc-800/70' : 'cursor-default'}`}
    >
      <div className="font-mono text-xs font-bold leading-tight">
        {code || '—'}
      </div>
      {(sub || tag) && (
        <div className="mt-0.5 whitespace-nowrap text-[9px] leading-tight text-zinc-400">
          {tag && <span className="mr-1">{tag}</span>}
          {sub}
        </div>
      )}
    </button>
  )
}

function SwapPanel({ kind, stopCandidates, destCandidates,
                     onSwapStopover, onSwapDestination }) {
  const isStop = kind === 'stopover'
  return (
    <div className="mt-2 rounded-lg border border-zinc-700 bg-zinc-900 p-3 shadow-xl">
      <div className="mb-2 text-[10px] font-bold uppercase tracking-wider text-zinc-400">
        {isStop ? 'Validated stopover paths' : 'Destination candidates'}
      </div>
      <div className="flex flex-wrap gap-1.5">
        {isStop
          ? (stopCandidates.length > 0
            ? stopCandidates.map(c => (
              <button
                key={c.city_id}
                type="button"
                onClick={() => onSwapStopover(c)}
                className="rounded border border-zinc-600 px-2.5 py-1.5 text-left
                           text-[11px] transition hover:border-amber-400/60 hover:bg-zinc-800"
              >
                <span className="font-semibold text-zinc-200">{c.name}</span>
                <span className="ml-1.5 font-mono text-amber-400">
                  ${Math.round(Number(c.per_person) || 0)} pp
                </span>
                <span className="ml-1.5 text-zinc-500">{c.days || 2}d</span>
              </button>
            ))
            : <span className="text-[11px] text-zinc-500">
                No validated stopover paths yet — run a discovery first.
              </span>)
          : (destCandidates.length > 0
            ? destCandidates.map(c => (
              <button
                key={c.city_id}
                type="button"
                onClick={() => onSwapDestination(c)}
                className="rounded border border-zinc-600 px-2.5 py-1 text-left
                           text-[11px] transition hover:border-emerald-400/60 hover:bg-zinc-800"
              >
                <span className="font-semibold text-zinc-200">{c.name}</span>
              </button>
            ))
            : <span className="text-[11px] text-zinc-500">
                No destination candidates yet — run a discovery first.
              </span>)}
      </div>
    </div>
  )
}

function legLabel(leg) {
  const nums = (leg?.flight_numbers || []).join('/')
  return nums || '\u2708'
}

function DagEditor({ optionB, origin, stopoverCandidates = [],
                     destinationCandidates = [],
                     onSwapStopover, onSwapDestination }) {
  const wrapRef = useRef(null)
  const [panel, setPanel] = useState(null) // 'stopover' | 'destination' | null
  const locked = !!optionB?.explorer

  const stopId = optionB?.stopover?.city_id
  const destId = optionB?.final?.city_id

  // Close the panel whenever the displayed path changes
  useEffect(() => { setPanel(null) }, [stopId, destId])

  // Click-outside closes the swap panel
  useEffect(() => {
    if (!panel) return undefined
    const onDown = (e) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target)) {
        setPanel(null)
      }
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [panel])

  if (!optionB || !origin) return null

  const oCode = origin.code || origin
  const stop = optionB.stopover
  const final = optionB.final
  // Merged outbound carries legs 1+2: first number belongs to origin→stop,
  // the rest to stop→destination.
  const outLegs = normalizeLegs(optionB.outbound)
  const inLegs = normalizeLegs(optionB.inbound)
  const outNums = outLegs.flatMap(l => l?.flight_numbers || [])
  const leg1Nums = outNums.slice(0, 1)
  const leg2Nums = outNums.slice(1)

  return (
    <div ref={wrapRef} className="relative">
      {locked && (
        <div className="mb-1 text-right">
          <span className="rounded bg-cyan-400/10 px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wider text-cyan-300">
            Explorer booked
          </span>
        </div>
      )}

      <div className="flex flex-wrap items-center justify-center gap-y-2 py-1">
        <Chip tone="origin" code={oCode} sub="depart" />
        <Arrow label={legLabel({ flight_numbers: leg1Nums })} />
        <Chip
          tone="stop"
          code={stop?.code || stop?.name || '—'}
          sub={`${stop?.days || 0}d stopover`}
          tag="◈"
          onClick={locked ? undefined : () => setPanel(
            p => p === 'stopover' ? null : 'stopover')}
          active={panel === 'stopover'}
        />
        <Arrow label={legLabel({ flight_numbers: leg2Nums })} />
        <Chip
          tone="dest"
          code={final?.code || final?.name || '—'}
          sub="destination"
          tag="⚑"
          onClick={locked ? undefined : () => setPanel(
            p => p === 'destination' ? null : 'destination')}
          active={panel === 'destination'}
        />
        <Arrow label={legLabel(inLegs[0])} />
        <Chip tone="home" code={oCode} sub="home" />
      </div>

      {panel && !locked && (
        <SwapPanel
          kind={panel}
          stopCandidates={stopoverCandidates}
          destCandidates={destinationCandidates}
          onSwapStopover={onSwapStopover}
          onSwapDestination={onSwapDestination}
        />
      )}
    </div>
  )
}

export default memo(DagEditor)
