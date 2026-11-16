'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../lib/api'
import Rail from '../components/Rail'
import Terminal from '../components/Terminal'
import { Panel } from '../components/Panel'
import Pill from '../components/Pill'
import Toast from '../components/Toast'
import AddAgentModal from '../components/AddAgentModal'
import DecisionModal from '../components/DecisionModal'
import DagEditor from '../components/DagEditor'

/* ----------------------------------------------------------------- helpers */

function surfacePhase(state) {
  if (!state) return 'idle'
  if (state.booked) return 'booked'
  if (state.worker_error) return 'error'
  if (state.synthesis || state.decision != null) return 'decide'
  if (state.chosen_graph || state.graph) return 'graph'
  if (state.cards && state.cards.length > 0) return 'inventory'
  if (state.date?.agreed || state.agreed_date) return 'scout'
  if (state.members && state.members.length > 0) return 'date'
  return 'feed'
}

function planeStatus(state) {
  if (!state) return 'waiting'
  if (state.booked) return 'boarded'
  if (state.decision != null) return 'ready'
  return 'waiting'
}

function bubbleMap(state) {
  const map = {}
  if (!state?.bubbles) return () => null
  for (const b of state.bubbles) {
    map[b.member] = b
  }
  return (name) => map[name] || null
}

function FlightLeg({ label, tone, leg }) {
  if (!leg) return null
  return (
    <div className="space-y-1">
      <div className={`flex items-center gap-2 font-pixel text-[11px] ${tone}`}>
        <span className="font-bold">{label}</span>
        <span className="text-ink">{leg.origin} → {leg.destination}</span>
        {leg.carriers?.length > 0 && (
          <span className="text-ink2">{leg.carriers.join(', ')}</span>
        )}
      </div>
      <div className="flex flex-wrap items-center gap-x-3 font-pixel text-[11px] text-ink2">
        <span>{leg.date}</span>
        {leg.flight_numbers?.length > 0 && (
          <span>{leg.flight_numbers.join(' + ')}</span>
        )}
        {leg.elapsed_hours > 0 && (
          <span className="text-ink3/60">{fmtElapsed(leg.elapsed_hours)}</span>
        )}
        {leg.price > 0 && (
          <span className="text-warn">${leg.price.toFixed(2)}</span>
        )}
      </div>
      {leg.segments?.map((s, si) => (
        <div key={si} className="ml-4 font-pixel text-[10px] text-ink3">
          {s.dep_airport} <span className="text-ink3/50">{fmtSegTime(s.dep_time)}</span>
          {' → '}
          {s.arr_airport} <span className="text-ink3/50">{fmtSegTime(s.arr_time)}</span>
          {s.flight_number && <span className="text-ink3/40"> ({s.flight_number})</span>}
        </div>
      ))}
    </div>
  )
}

function fmtSegTime(s) {
  if (!s) return ''
  if (/^\d{8}/.test(s)) {
    const m = parseInt(s.slice(4, 6))
    const d = parseInt(s.slice(6, 8))
    const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']
    const time = s.length > 8 ? s.slice(9) : ''
    return `${months[m - 1] || ''} ${d} ${time}`.trim()
  }
  return s
}

function fmtElapsed(h) {
  const hrs = Math.floor(h)
  const mins = Math.round((h - hrs) * 60)
  return mins > 0 ? `${hrs}h ${mins}min` : `${hrs}h`
}

const MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']
const DAYS = ['Mo','Tu','We','Th','Fr','Sa','Su']

function DepartureCalendar({ agreedDate, onSelect, onClose }) {
  const [month, setMonth] = useState(() => {
    if (!agreedDate) return new Date()
    const y = parseInt(agreedDate.slice(0, 4))
    const m = parseInt(agreedDate.slice(4, 6)) - 1
    return new Date(y, m, 1)
  })
  const [picked, setPicked] = useState(null)

  const firstDay = new Date(month.getFullYear(), month.getMonth(), 1)
  const daysInMonth = new Date(month.getFullYear(), month.getMonth() + 1, 0).getDate()
  let startDow = firstDay.getDay() - 1
  if (startDow < 0) startDow = 6

  const prevMonth = () => setMonth(new Date(month.getFullYear(), month.getMonth() - 1, 1))
  const nextMonth = () => setMonth(new Date(month.getFullYear(), month.getMonth() + 1, 1))

  const cells = []
  for (let i = 0; i < startDow; i++) cells.push(null)
  for (let d = 1; d <= daysInMonth; d++) cells.push(d)

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-canvas/80 backdrop-blur-sm">
      <div className="hud w-[320px] p-4">
        <div className="mb-3 flex items-center justify-between">
          <button className="btn-ghost !px-2 !py-1" onClick={prevMonth}>&#8592;</button>
          <span className="font-pixel text-[12px] text-ink font-bold">
            {MONTHS[month.getMonth()]} {month.getFullYear()}
          </span>
          <button className="btn-ghost !px-2 !py-1" onClick={nextMonth}>&#8594;</button>
        </div>
        <div className="grid grid-cols-7 gap-1">
          {DAYS.map(d => (
            <div key={d} className="py-1 text-center font-pixel text-[9px] text-ink3">{d}</div>
          ))}
          {cells.map((day, i) => {
            if (!day) return <div key={`e${i}`} />
            const dateStr = `${month.getFullYear()}${String(month.getMonth() + 1).padStart(2, '0')}${String(day).padStart(2, '0')}`
            const isPicked = picked === dateStr
            return (
              <button
                key={i}
                className={`py-1 text-center font-pixel text-[11px] rounded-sm transition-colors ${
                  isPicked ? 'bg-ok text-canvas font-bold' : 'text-ink hover:bg-ok/20'
                }`}
                onClick={() => setPicked(dateStr)}
              >
                {day}
              </button>
            )
          })}
        </div>
        <div className="mt-3 flex gap-2">
          <button
            className="btn-primary flex-1"
            disabled={!picked}
            onClick={() => { if (picked) onSelect(picked) }}
          >
            Search this date
          </button>
          <button className="btn-ghost" onClick={onClose}>Cancel</button>
        </div>
      </div>
    </div>
  )
}

function ItineraryChangeModal({ oldBooked, newCards, origin, onProceed, onClose }) {
  const newCard = newCards?.[0]
  if (!oldBooked || !newCard) return null

  const oldTotal = Number(oldBooked.total || 0)
  const newTotal = Number(newCard.group_total || 0)
  const diff = newTotal - oldTotal

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-canvas/80 backdrop-blur-sm">
      <div className="hud w-[480px] max-h-[90vh] overflow-y-auto p-5">
        <h3 className="font-pixel text-[14px] font-bold text-ink mb-4">
          Itinerary Change
        </h3>
        <div className="grid grid-cols-2 gap-4">
          <div className="space-y-2">
            <div className="font-pixel text-[10px] text-danger/70 font-bold uppercase">Current</div>
            <div className="font-pixel text-[13px] text-ink font-bold">
              {oldBooked.flight}
            </div>
            {oldBooked.outbound && (
              <div className="font-pixel text-[11px] text-ink2">
                {oldBooked.outbound.origin} → {oldBooked.outbound.destination}
                <div className="text-ink3">{oldBooked.outbound.date}</div>
                <div className="text-ink3">{fmtElapsed(oldBooked.outbound.elapsed_hours)}</div>
              </div>
            )}
            {oldBooked.inbound && (
              <div className="font-pixel text-[11px] text-ink2">
                {oldBooked.inbound.origin} → {oldBooked.inbound.destination}
                <div className="text-ink3">{oldBooked.inbound.date}</div>
                <div className="text-ink3">{fmtElapsed(oldBooked.inbound.elapsed_hours)}</div>
              </div>
            )}
            <div className="font-pixel text-[12px] text-warn">
              ${oldTotal.toFixed(2)}
            </div>
          </div>
          <div className="space-y-2">
            <div className="font-pixel text-[10px] text-ok/70 font-bold uppercase">New</div>
            <div className="font-pixel text-[13px] text-ink font-bold">
              {newCard.destination || newCard.destination_id}
            </div>
            {newCard.outbound && (
              <div className="font-pixel text-[11px] text-ink2">
                {newCard.outbound.origin} → {newCard.outbound.destination}
                <div className="text-ink3">{newCard.outbound.date}</div>
                <div className="text-ink3">{fmtElapsed(newCard.outbound.elapsed_hours)}</div>
              </div>
            )}
            {newCard.inbound && (
              <div className="font-pixel text-[11px] text-ink2">
                {newCard.inbound.origin} → {newCard.inbound.destination}
                <div className="text-ink3">{newCard.inbound.date}</div>
                <div className="text-ink3">{fmtElapsed(newCard.inbound.elapsed_hours)}</div>
              </div>
            )}
            <div className="font-pixel text-[12px] text-warn">
              ${newTotal.toFixed(2)}
            </div>
          </div>
        </div>
        <div className="hud-divider mt-4" />
        <div className="mt-3 flex items-center justify-between font-pixel text-[12px]">
          <span className="text-ink2">Cost difference</span>
          <span className={diff > 0 ? 'text-danger font-bold' : diff < 0 ? 'text-ok font-bold' : 'text-ink'}>
            {diff > 0 ? '+' : ''}{diff.toFixed(2)} SGD
          </span>
        </div>
        <div className="mt-4 flex gap-2">
          <button className="btn-primary flex-1" onClick={onProceed}>
            Proceed with rebooking
          </button>
          <button className="btn-ghost" onClick={onClose}>Cancel</button>
        </div>
      </div>
    </div>
  )
}

/* ------------------------------------------------------------------- shell */
export default function Home() {
  const [state, setState] = useState(null)
  const [toast, setToast] = useState(null)
  const [addOpen, setAddOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const [calOpen, setCalOpen] = useState(false)
  const [prevBooked, setPrevBooked] = useState(null)
  const [changeModal, setChangeModal] = useState(false)
  const [selectedOptionB, setSelectedOptionB] = useState(null)
  const [minDays, setMinDays] = useState(2)

  const poll = useCallback(async () => {
    try {
      const s = await api.getState()
      setState(s)
    } catch {
      // backend not running yet
    }
  }, [])

  useEffect(() => {
    poll()
    const id = setInterval(poll, 1000)
    return () => clearInterval(id)
  }, [poll])

  const showToast = useCallback((text, kind = 'ok') => setToast({ text, kind }), [])

  // Primitive flag — stable dep for the DAG swap handler callbacks
  const bookedExplorer = !!state?.booked?.explorer

  const run = async (fn, successMsg) => {
    setLoading(true)
    try {
      const result = await fn()
      await poll()
      if (result && result.error) {
        showToast(result.error, 'error')
      } else if (successMsg) {
        showToast(successMsg)
      }
      return result
    } catch (err) {
      showToast(err.message || 'Request failed', 'error')
    } finally {
      setLoading(false)
    }
  }

  const handleSelectOptionB = async (optB, idx) => {
    setSelectedOptionB(optB)
    // Option B cards are appended after the direct cards
    const directCount = cards.filter(c => !c.explorer).length
    await run(() => api.selectCard(directCount + idx), 'Explorer route selected')
  }

  const handleConfirmOptionB = async () => {
    const result = await run(api.confirm, 'Explorer route booked')
    if (!result || !result.error) {
      setSelectedOptionB(null)
    }
  }

  const handleConfirmDirect = async () => {
    await run(api.confirm, 'Booking confirmed')
  }

  const handleSwapStopover = useCallback(async (candidate) => {
    // Allow swap when either selectedOptionB is set OR booked.explorer is true
    if (!selectedOptionB && !bookedExplorer) return
    const cityName = candidate.name || candidate.city_id
    setLoading(true)
    showToast(`Searching flights via ${cityName}...`)
    try {
      const result = await api.swapStopover(candidate.city_id, cityName, minDays)
      if (result.error) {
        showToast(result.error, 'error')
      } else if (result.instant) {
        showToast(`Switched path via ${cityName} instantly (validated route)`)
        if (result.new_option) {
          setSelectedOptionB(result.new_option)
        }
      } else {
        showToast(`Found ${result.cards} route${result.cards !== 1 ? 's' : ''} via ${cityName}`)
        // Update local selection to the new option
        if (result.new_option) {
          setSelectedOptionB(result.new_option)
        }
      }
      await poll()
    } catch (err) {
      showToast(err.message || 'Swap failed', 'error')
    } finally {
      setLoading(false)
    }
  }, [selectedOptionB, bookedExplorer, minDays, poll, showToast])

  const handleSwapDestination = useCallback(async (candidate) => {
    // Allow swap when either selectedOptionB is set OR booked.explorer is true
    if (!selectedOptionB && !bookedExplorer) return
    const cityName = candidate.name || candidate.city_id
    setLoading(true)
    showToast(`Searching flights to ${cityName}...`)
    try {
      const result = await api.swapDestination(candidate.city_id, cityName, minDays)
      if (result.error) {
        showToast(result.error, 'error')
      } else {
        const viaStop = result.stopover ? ` via ${result.stopover}` : ''
        showToast(`Found ${result.cards} route${result.cards !== 1 ? 's' : ''} to ${cityName}${viaStop}`)
        // Update local selection to the new option
        if (result.new_option) {
          setSelectedOptionB(result.new_option)
        }
      }
      await poll()
    } catch (err) {
      showToast(err.message || 'Swap failed', 'error')
    } finally {
      setLoading(false)
    }
  }, [selectedOptionB, bookedExplorer, minDays, poll, showToast])

  const handleRunAutonomous = async () => {
    setLoading(true)
    showToast('Running autonomous: date consensus + discovery...')
    try {
      const result = await api.runAutonomous(minDays)
      if (result.error) {
        showToast(result.error, 'error')
      } else {
        showToast(`Autonomous run complete. Date: ${result.agreed_date}. Discovery started.`)
      }
      await poll()
    } catch (err) {
      showToast(err.message || 'Autonomous run failed', 'error')
    } finally {
      setLoading(false)
    }
  }

  const handleCancelAndRebook = async () => {
    setLoading(true)
    showToast('Cancelling previous booking...')
    try {
      const result = await api.cancelBooking()
      if (result && result.error) {
        showToast(result.error, 'error')
      } else {
        showToast('Booking cancelled. Confirm your selected flight or pick another.')
      }
      await poll()
      return result
    } catch (err) {
      showToast(err.message || 'Cancel failed', 'error')
      return null
    } finally {
      setLoading(false)
    }
  }

  const handleChangeDeparture = async (dateStr) => {
    setCalOpen(false)
    setLoading(true)
    try {
      setPrevBooked(state?.booked ? { ...state.booked } : null)
      await api.constraint({ kind: 'date', date: dateStr })
      await api.discover()
      showToast(`Re-searching for ${dateStr}`)
      setChangeModal(true)
    } catch (err) {
      showToast(err.message || 'Failed to change date', 'error')
    } finally {
      setLoading(false)
    }
  }

  const handleProceedRebook = async () => {
    setChangeModal(false)
    await run(async () => {
      await api.selectCard(0)
      await api.confirm()
    }, 'Rebooking confirmed')
    setPrevBooked(null)
  }

  const phase = surfacePhase(state)
  const bubbleFor = bubbleMap(state)
  const members = state?.members || []
  const cards = state?.cards || []
  const shortlist = state?.shortlist || []
  const graph = state?.chosen_graph || state?.graph
  const itineraryOptions = state?.itinerary_options
  const selectedCard = state?.selected_card ?? null
  const discoveryErrors = state?.discovery_errors || []
  const logTail = state?.log_tail || []

  // Stable props for the memoized DagEditor — the 1-second poll must not
  // re-render the graph when the itinerary hasn't changed.
  const bookedJson = state?.booked ? JSON.stringify(state.booked) : ''
  const shortlistIds = shortlist.map(r => r.cityId).join(',')

  const dagOptionB = useMemo(() => {
    if (state?.booked?.explorer) {
      return {
        stopover: state.booked.stopover,
        final: { name: state.booked.flight, city_id: state.booked.destination_id },
        outbound: state.booked.outbound,
        inbound: state.booked.inbound,
        label: state.booked.flight,
        per_person: state.booked.per_person,
        group_total: state.booked.total,
      }
    }
    return selectedOptionB
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bookedJson, selectedOptionB])

  // Stopover swap candidates = the backend's validated paths: only cities
  // the agent already found real routes through, so every swap returns a
  // new path (applied instantly from cached search results).
  const stopoverPathsKey = useMemo(() => {
    try {
      return JSON.stringify(itineraryOptions?.stopover_paths || [])
    } catch { return '' }
  }, [itineraryOptions])
  const dagStopoverCandidates = useMemo(
    () => itineraryOptions?.stopover_paths || [],
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [stopoverPathsKey]
  )

  const dagDestinationCandidates = useMemo(
    () => shortlist
      .filter(r => r.cityId !== state?.origin)
      .map(r => ({ city_id: r.cityId, name: r.cityName, days: minDays })),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [shortlistIds, state?.origin]
  )

  // Clear explorer selection when no card is selected (e.g. after reset)
  useEffect(() => {
    if (selectedCard === null) {
      setSelectedOptionB(null)
    }
  }, [selectedCard])

  // Auto re-discover when minDays slider changes (debounced, only if options exist)
  const loadingRef = useRef(loading)
  const discoveringRef = useRef(state?.discovering)
  useEffect(() => { loadingRef.current = loading }, [loading])
  useEffect(() => { discoveringRef.current = state?.discovering }, [state?.discovering])

  useEffect(() => {
    if (!itineraryOptions?.options_b) return
    const t = setTimeout(() => {
      if (!loadingRef.current && !discoveringRef.current) {
        run(() => api.discover(minDays), 'Re-searching routes')
      }
    }, 600)
    return () => clearTimeout(t)
  }, [minDays])

  return (
    <div className="flex min-h-screen">
      {/* ── sidebar ─────────────────────────────────────────────── */}
      <aside className="fixed inset-y-0 left-0 z-20 hidden w-[264px] border-r
                        border-glass-edge bg-canvas/80 backdrop-blur-2xl lg:flex
                        lg:flex-col lg:p-4">
        <div className="mb-6 flex items-center gap-2">
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" className="shrink-0 text-brand-400">
            <path d="M21 16v-2l-8-5V3.5a1.5 1.5 0 0 0-3 0V9l-8 5v2l8-2.5V19l-2 1.5V22l3.5-1 3.5 1v-1.5L13 19v-5.5l8 2.5z" fill="currentColor"/>
          </svg>
          <div>
            <span className="font-pixel text-[13px] font-bold tracking-wider text-brand-400">
              PARTYNERARY
            </span>
            <div className="font-pixel text-[9px] text-ink3/50 tracking-wide">
              group flight orchestration
            </div>
          </div>
        </div>
        <Rail state={state} />

      </aside>

      {/* ── main column ─────────────────────────────────────────── */}
      <div className="flex min-w-0 flex-1 flex-col lg:ml-[264px]">
        {/* top bar */}
        <header className="sticky top-0 z-30 flex h-16 items-center gap-3
                           border-b border-glass-edge bg-canvas/70 px-4
                           backdrop-blur-2xl lg:px-6">
          <div className="ml-auto flex items-center gap-2">
            <button
              className="btn-ghost"
              onClick={() => run(api.reset, 'State reset')}
              disabled={loading}
            >
              Reset
            </button>
            <button
              className="btn-primary"
              onClick={() => setAddOpen(true)}
            >
              + Agent
            </button>
          </div>
        </header>

        {/* error bar */}
        {state?.worker_error && (
          <div className="border-b border-danger/30 bg-danger/10 px-4 py-2
                          font-pixel text-[11px] text-danger lg:px-6">
            {state.worker_error}
          </div>
        )}

        {/* content */}
        <main className="mx-auto w-full max-w-[1400px] p-4 lg:p-6">
          <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_380px]">

            {/* ── left column ───────────────────────────────────── */}
            <div className="space-y-4">
              {/* Terminal lounge */}
              <Terminal
                state={state || { members: [] }}
                bubbleFor={bubbleFor}
                planeStatus={planeStatus(state)}
                selectedCard={selectedCard != null ? cards[selectedCard] : null}
              />

              {/* Flight deck button row */}
              <div className="hud flex flex-wrap gap-2 p-3">
                <button
                  className="btn-primary flex-1"
                  onClick={handleRunAutonomous}
                  disabled={loading || members.length === 0}
                >
                  {state?.agreed_date && cards.length > 0
                    ? 'Re-run discovery'
                    : state?.agreed_date
                    ? 'Search flights'
                    : 'Run autonomous'}
                </button>
                {state?.booked && selectedOptionB && (
                  <button
                    className="btn-ghost text-danger/80 hover:text-danger"
                    onClick={handleCancelAndRebook}
                    disabled={loading}
                  >
                    Cancel current booking
                  </button>
                )}
              </div>

              {/* Itinerary Options */}
              <Panel id="panel-graph" title="Itinerary options">
                {!itineraryOptions ? (
                  <p className="text-sm text-ink3">
                    Run discovery to see itinerary options.
                  </p>
                ) : (
                  <div className="space-y-4">
                    {/* Option A — Best direct */}
                    <div>
                      <div className="mb-2 flex items-center gap-2">
                        <span className="inline-block h-5 w-5 rounded-sm bg-ok/20 font-pixel text-[10px] font-bold text-ok text-center leading-5">A</span>
                        <span className="font-pixel text-[11px] text-ink font-bold">Best direct</span>
                        <span className="ml-auto font-pixel text-[12px] text-warn font-bold">
                          ${Number(itineraryOptions.option_a.per_person).toFixed(2)} pp
                        </span>
                      </div>
                      {/* Node graph */}
                      <div className="flex items-center gap-2 px-2 py-3">
                        <div className="flex flex-col items-center">
                          <div className="h-3 w-3 rounded-full bg-brand-400 ring-2 ring-brand-400/30" />
                          <span className="mt-1 font-pixel text-[10px] text-ink font-bold">{state?.origin}</span>
                        </div>
                        <div className="flex-1 relative">
                          <div className="border-t border-ok/40" />
                          <div className="absolute -top-4 left-1/2 -translate-x-1/2 font-pixel text-[9px] text-ok whitespace-nowrap">
                            {fmtElapsed(itineraryOptions.option_a.outbound?.elapsed_hours || 0)}
                          </div>
                          <svg className="absolute -right-1 top-1/2 -translate-y-1/2 text-ok" width="8" height="8" viewBox="0 0 8 8">
                            <path d="M0 0 L8 4 L0 8Z" fill="currentColor" />
                          </svg>
                        </div>
                        <div className="flex flex-col items-center">
                          <div className="h-3 w-3 rounded-full bg-ok ring-2 ring-ok/30" />
                          <span className="mt-1 font-pixel text-[10px] text-ink font-bold">
                            {itineraryOptions.option_a.destination_id}
                          </span>
                        </div>
                      </div>
                      <div className="font-pixel text-[10px] text-ink3 text-center">
                        {itineraryOptions.option_a.outbound?.date} · {itineraryOptions.option_a.destination}
                      </div>
                    </div>

                    <div className="hud-divider" />

                    {/* Min stopover days slider */}
                    <div className="flex items-center gap-3 px-1">
                      <span className="font-pixel text-[10px] text-ink2 whitespace-nowrap">Min layover</span>
                      <input
                        type="range"
                        min={1} max={7} step={1}
                        value={minDays}
                        onChange={e => setMinDays(Number(e.target.value))}
                        className="flex-1 accent-brand-500 h-1"
                      />
                      <span className="font-pixel text-[11px] text-ink w-12 text-right">
                        {minDays} day{minDays > 1 ? 's' : ''}
                      </span>
                    </div>

                    <div className="hud-divider" />

                    {/* Option B — Explorer routes */}
                    <div>
                      <div className="mb-2 flex items-center gap-2">
                        <span className="inline-block h-5 w-5 rounded-sm bg-brand-500/20 font-pixel text-[10px] font-bold text-brand-400 text-center leading-5">B</span>
                        <span className="font-pixel text-[11px] text-ink font-bold">Explorer routes</span>
                      </div>
                      <div className="space-y-2">
                        {itineraryOptions.options_b?.map((optB, i) => (
                          <div
                            key={i}
                            className={`hud p-3 cursor-pointer transition-all ${
                              selectedOptionB === optB
                                ? 'ring-2 ring-brand-400/60 brightness-110'
                                : 'hover:brightness-105'
                            }`}
                            onClick={() => handleSelectOptionB(optB, i)}
                          >
                            <div className="flex items-center justify-between mb-1">
                              <span className="font-pixel text-[11px] text-ink font-bold">
                                {optB.label}
                              </span>
                              <span className="font-pixel text-[11px] text-warn">
                                ${Number(optB.per_person).toFixed(2)} pp
                              </span>
                            </div>
                            {/* Route: Origin → Stop (N days) → Final → Origin */}
                            <div className="flex items-center gap-1 font-pixel text-[10px] text-ink2 mb-1">
                              <span>{state?.origin}</span>
                              <span className="text-ink3/50">→</span>
                              <span className="text-brand-400">{optB.stopover.name}</span>
                              <span className="rounded bg-brand-500/15 px-1.5 py-0.5 font-pixel text-[8px] text-brand-400">
                                {optB.stopover.days}d
                              </span>
                              <span className="text-ink3/50">→</span>
                              <span>{optB.final.name}</span>
                              <span className="text-ink3/50">→</span>
                              <span>{state?.origin}</span>
                            </div>
                            <div className="flex items-center justify-between">
                              <span className={`font-pixel text-[10px] ${Number(optB.savings) >= 0 ? 'text-ok' : 'text-amber-400'}`}>
                                {Number(optB.savings) >= 0
                                  ? `Save $${Number(optB.savings).toFixed(0)} pp vs separate flights`
                                  : `+$${Math.abs(Number(optB.savings)).toFixed(0)} pp vs separate flights`}
                              </span>
                              {selectedOptionB === optB && (
                                <span className="font-pixel text-[9px] text-brand-400 font-bold">SELECTED</span>
                              )}
                            </div>
                            <div className="mt-1 font-pixel text-[10px] text-ink3">
                              {optB.why}
                            </div>
                            {/* Segments detail */}
                            {optB.outbound?.segments?.map((s, si) => (
                              <div key={si} className="ml-2 mt-1 font-pixel text-[9px] text-ink3/60">
                                {s.dep_airport} <span className="text-ink3/40">{fmtSegTime(s.dep_time)}</span>
                                {' → '}
                                {s.arr_airport} <span className="text-ink3/40">{fmtSegTime(s.arr_time)}</span>
                                {s.flight_number && <span className="text-ink3/30"> ({s.flight_number})</span>}
                              </div>
                            ))}
                          </div>
                        ))}
                      </div>
                    </div>
                  </div>
                )}
              </Panel>

              {/* Manage Itinerary — DAG editor */}
              <Panel id="panel-manage" title="Manage itinerary">
                {!selectedOptionB && !state?.booked?.explorer ? (
                  <p className="text-sm text-ink3">
                    Select an Explorer route (Option B) to manage its itinerary.
                  </p>
                ) : (
                  <DagEditor
                    optionB={dagOptionB}
                    origin={state?.origin}
                    stopoverCandidates={dagStopoverCandidates}
                    destinationCandidates={dagDestinationCandidates}
                    onSwapStopover={handleSwapStopover}
                    onSwapDestination={handleSwapDestination}
                  />
                )}
              </Panel>

              {/* Flight cards from discovery engine */}
              <Panel id="panel-flights" title="Available flights">
                {state?.discovering ? (
                  <p className="font-pixel text-[11px] text-brand-400 animate-pulse">
                    Searching Atlas for flights...
                  </p>
                ) : cards.length === 0 ? (
                  <p className="text-sm text-ink3">
                    No flights yet. 
                  </p>
                ) : (
                  <div className="space-y-3">
                    {cards.map((c, i) => (
                      <div
                        key={i}
                        className={`hud relative p-4 accent-bar cursor-pointer transition-all ${
                          selectedCard === i
                            ? 'before:bg-ok ring-2 ring-ok/50 brightness-110'
                            : 'before:bg-brand-500 hover:brightness-105'
                        }`}
                        onClick={() => { setSelectedOptionB(null); run(() => api.selectCard(i), 'Flight selected') }}
                      >
                        {/* Header — destination, carriers, vibe */}
                        <div className="flex items-start justify-between gap-2">
                          <div>
                            <span className="font-pixel text-[14px] font-bold text-ink">
                              {c.destination || c.destination_id}
                            </span>
                            {c.carriers?.length > 0 && (
                              <div className="font-pixel text-[10px] text-ink3 mt-0.5">
                                {c.carriers.join(' · ')}
                              </div>
                            )}
                          </div>
                          <div className="text-right shrink-0">
                            <div className="font-pixel text-[14px] font-bold text-warn">
                              ${Number(c.per_person).toFixed(2)}
                            </div>
                            <div className="font-pixel text-[10px] text-ink3">per person</div>
                          </div>
                        </div>

                        {/* Outbound + Return legs */}
                        <div className="mt-3 space-y-3">
                          <FlightLeg label="OUTBOUND" tone="text-ok" leg={c.outbound} />
                          {c.outbound && c.inbound && (
                            <div className="hud-divider" />
                          )}
                          <FlightLeg label="RETURN" tone="text-brand-400" leg={c.inbound} />
                        </div>

                        {/* Footer — pricing, seats, cost_ref */}
                        <div className="hud-divider mt-3" />
                        <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
                          <div className="flex items-center gap-3 font-pixel text-[11px]">
                            <span className="text-ink2">
                              Group: <span className="text-warn">${Number(c.group_total).toFixed(2)}</span>
                            </span>
                            <span className="text-ink3">
                              {c.seats} seat{c.seats !== 1 ? 's' : ''} remaining
                            </span>
                          </div>
                          <div className="flex items-center gap-2">
                            {c.cost_ref && (
                              <span className="font-pixel text-[9px] text-ink3/40">
                                {c.cost_ref}
                              </span>
                            )}
                            {selectedCard === i ? (
                              <span className="font-pixel text-[10px] font-bold text-ok">
                                SELECTED
                              </span>
                            ) : (
                              <span className="font-pixel text-[10px] text-brand-400">
                                Click to select
                              </span>
                            )}
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                )}

                {/* Discovery errors */}
                {discoveryErrors.length > 0 && (
                  <div className="mt-3 space-y-1 border-t border-danger/20 pt-2">
                    <p className="font-pixel text-[10px] text-danger/70">
                      Atlas returned errors for {discoveryErrors.length} destination(s):
                    </p>
                    {discoveryErrors.slice(0, 3).map((e, i) => (
                      <div key={i} className="font-pixel text-[10px] text-ink3/60">
                        {e.destination}: {e.error?.slice(0, 80)}
                      </div>
                    ))}
                    {discoveryErrors.length > 3 && (
                      <div className="font-pixel text-[10px] text-ink3/40">
                        +{discoveryErrors.length - 3} more
                      </div>
                    )}
                  </div>
                )}
              </Panel>
            </div>

            {/* ── right rail, 380px ─────────────────────────────── */}
            <div className="space-y-4">
              {/* Passengers */}
              <Panel title="Passengers" accent={`${members.length} checked in`}>
                {members.length === 0 ? (
                  <p className="text-sm text-ink3">No agents yet.</p>
                ) : (
                  <div className="space-y-2">
                    {members.map((m) => (
                      <div key={m.name} className="flex items-center justify-between hud p-2">
                        <div>
                          <span className="font-pixel text-[12px] text-ink">{m.name}</span>
                          {m.budget != null && (
                            <span className="ml-2 font-pixel text-[11px] text-brand-400">
                              ${m.budget}
                            </span>
                          )}
                        </div>
                        <button
                          className="btn-ghost !px-2 !py-1 text-[10px]"
                          onClick={() => run(() => api.removeMember(m.name), `${m.name} removed`)}
                          disabled={loading}
                        >
                          remove
                        </button>
                      </div>
                    ))}
                  </div>
                )}
              </Panel>

              {/* Scout - engine recommendations */}
              <Panel id="panel-scout" title="Scout - engine recommendations">
                {state?.discovering ? (
                  <p className="font-pixel text-[11px] text-brand-400 animate-pulse">
                    Matching preferences to cities...
                  </p>
                ) : shortlist.length === 0 ? (
                  <p className="text-sm text-ink3">
                    Add agents with preferences and run discovery to see city matches.
                  </p>
                ) : (
                  <div className="space-y-2">
                    {shortlist.map((r, i) => (
                      <div key={i} className="hud p-3">
                        <div className="flex items-center justify-between">
                          <span className="font-pixel text-[12px] text-ink">
                            {r.cityName}
                            <span className="ml-1 text-ink3">({r.cityId})</span>
                          </span>
                          <span className="font-pixel text-[11px] text-brand-400">
                            {r.vibeScore.toFixed(3)}
                          </span>
                        </div>
                        <div className="mt-1 text-[11px] text-ink2">{r.why}</div>
                        {/* Multi-tag row: country + matched keywords + named badge */}
                        <div className="mt-1.5 flex flex-wrap gap-1">
                          <span className="tag">{r.country}</span>
                          {r.matched && r.matched.map((kw, ki) => (
                            <span key={ki} className="tag">{kw}</span>
                          ))}
                          {r.named > 0 && (
                            <span className="tag border-ok/40 bg-ok/10 text-ok">named</span>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </Panel>

              {/* Date consensus */}
              <Panel id="panel-date" title="Date consensus">
                {state?.agreed_date ? (
                  <div className="space-y-2">
                    <Pill tone="ok">Agreed: {state.agreed_date}</Pill>
                    {state.concession_round > 0 && (
                      <p className="font-pixel text-[11px] text-ink3">
                        Settled in {state.concession_round} round{state.concession_round > 1 ? 's' : ''}
                      </p>
                    )}
                  </div>
                ) : state?.failed ? (
                  <p className="text-sm text-danger">
                    {state.failed_reason || 'Negotiation failed'}
                  </p>
                ) : (
                  <p className="text-sm text-ink3">
                    No date agreed yet. Add agents and run to agreement, or pick a test date.
                  </p>
                )}

                {/* Animated discussion transcript */}
                {state?.moves?.length > 0 && (
                  <div className="mt-3 max-h-48 space-y-1 overflow-y-auto">
                    {state.moves.map((mv, mi) => (
                      <div
                        key={mi}
                        className="flex items-center gap-2 font-pixel text-[11px] animate-in fade-in slide-in-from-left-2"
                        style={{ animationDelay: `${mi * 40}ms` }}
                      >
                        <span className="text-ink3 w-4">R{mv.round}</span>
                        <span className={mv.withdrawn ? 'text-danger' : 'text-ink'}>
                          {mv.member}
                        </span>
                        {mv.withdrawn ? (
                          <span className="text-danger">withdrew</span>
                        ) : (
                          <>
                            <span className="text-ok">{mv.date}</span>
                            {mv.conceded_from && (
                              <span className="text-ink3/50">from {mv.conceded_from}</span>
                            )}
                          </>
                        )}
                      </div>
                    ))}
                  </div>
                )}

                {/* Test date buttons */}
                {members.length > 0 && !state?.agreed_date && (
                  <div className="mt-3">
                    <p className="hud-label mb-1.5">Test date options</p>
                    <div className="flex flex-wrap gap-1.5">
                      {['20260911', '20260925', '20261009', '20261023'].map((d) => (
                        <button
                          key={d}
                          className="btn-ghost !px-2 !py-1 text-[10px]"
                          onClick={() => run(
                            () => api.constraint({ kind: 'date', date: d }),
                            `Date set to ${d}`
                          )}
                          disabled={loading}
                        >
                          {d.slice(4, 6)}/{d.slice(6)}
                        </button>
                      ))}
                    </div>
                  </div>
                )}
              </Panel>

              {/* Authority */}
              <Panel id="panel-authority" title={state?.booked?.flight ? 'Manage booking' : 'Confirm booking'}>
                {state?.booked?.flight ? (
                  <div className="space-y-3">
                    <div className="flex items-center gap-2">
                      <Pill tone="ok">Booked</Pill>
                      {state.booked.explorer && (
                        <span className="font-pixel text-[10px] text-brand-400">Explorer route</span>
                      )}
                    </div>
                    {/* Flight summary */}
                    <div className="font-pixel text-[13px] font-bold text-ink">
                      {state.booked.flight}
                    </div>
                    {state.booked.outbound && (
                      <div className="font-pixel text-[11px] text-ink2">
                        {state.booked.outbound.origin} → {state.booked.outbound.destination}
                        <span className="ml-2 text-ink3">{state.booked.outbound.date}</span>
                        {state.booked.outbound.flight_numbers?.length > 0 && (
                          <span className="ml-2 text-warn">{state.booked.outbound.flight_numbers.join(' + ')}</span>
                        )}
                      </div>
                    )}
                    {state.booked.inbound && (
                      <div className="font-pixel text-[11px] text-ink2">
                        {state.booked.inbound.origin} → {state.booked.inbound.destination}
                        <span className="ml-2 text-ink3">{state.booked.inbound.date}</span>
                        {state.booked.inbound.flight_numbers?.length > 0 && (
                          <span className="ml-2 text-warn">{state.booked.inbound.flight_numbers.join(' + ')}</span>
                        )}
                      </div>
                    )}
                    {state.booked.stopover && (
                      <div className="inline-flex items-center gap-1 rounded-full border border-brand-400/40 bg-brand-400/10 px-2 py-0.5 font-pixel text-[10px] text-brand-400">
                        Via {state.booked.stopover.name} ({state.booked.stopover.days}d)
                      </div>
                    )}
                    <div className="hud-divider" />
                    {/* Management actions */}
                    <div className="space-y-2">
                      <button
                        className="btn-ghost w-full text-left"
                        onClick={() => setCalOpen(true)}
                        disabled={loading}
                      >
                        Change departure date
                      </button>
                      <button
                        className="btn-ghost w-full text-left text-danger/80 hover:text-danger"
                        onClick={handleCancelAndRebook}
                        disabled={loading}
                      >
                        Cancel booking
                      </button>
                    </div>
                  </div>
                ) : selectedOptionB ? (
                  <div className="space-y-2">
                    <Pill tone="warn">Explorer route selected</Pill>
                    <p className="font-pixel text-[11px] text-ink2">
                      {selectedOptionB.label} — ${Number(selectedOptionB.per_person).toFixed(2)} pp
                    </p>
                    {state?.booked ? (
                      <>
                        <p className="font-pixel text-[10px] text-warn">
                          This will replace your current booking.
                        </p>
                        <button
                          className="btn-primary w-full"
                          onClick={async () => {
                            const r = await handleCancelAndRebook()
                            if (r && !r.error) await handleConfirmOptionB()
                          }}
                          disabled={loading}
                        >
                          Cancel current &amp; book new
                        </button>
                      </>
                    ) : (
                      <button
                        className="btn-primary w-full"
                        onClick={handleConfirmOptionB}
                        disabled={loading}
                      >
                        Confirm explorer route
                      </button>
                    )}
                  </div>
                ) : state?.decision != null ? (
                  <div className="space-y-2">
                    <Pill tone="warn">Flight selected</Pill>
                    <p className="font-pixel text-[11px] text-ink2">
                      Ready to confirm your booking.
                    </p>
                    <button
                      className="btn-primary w-full"
                      onClick={handleConfirmDirect}
                      disabled={loading}
                    >
                      Confirm &amp; book this flight
                    </button>
                  </div>
                ) : state?.synthesis ? (
                  <div className="space-y-2">
                    <p className="text-sm text-ink2">Synthesis ready - awaiting decision.</p>
                    {state.synthesis.option1 && (
                      <button
                        className="btn-ghost w-full text-left"
                        onClick={() => run(() => api.decide('option1'), 'Option 1 chosen')}
                        disabled={loading}
                      >
                        Choose option 1
                      </button>
                    )}
                    {state.synthesis.option2 && (
                      <button
                        className="btn-ghost w-full text-left"
                        onClick={() => run(() => api.decide('option2'), 'Option 2 chosen')}
                        disabled={loading}
                      >
                        Choose option 2
                      </button>
                    )}
                  </div>
                ) : cards.length > 0 ? (
                  <p className="text-sm text-ink3">
                    Click a flight card below to select it for booking.
                  </p>
                ) : (
                  <p className="text-sm text-ink3">
                    Run discovery to see available flights, then select one to book.
                  </p>
                )}
              </Panel>

              {/* Move the departure */}
              <Panel title="Move the departure">
                <p className="text-sm text-ink3">
                  Pick a new departure date to re-search flights.
                </p>
                <div className="mt-2">
                  <button
                    className="btn-ghost w-full"
                    onClick={() => setCalOpen(true)}
                    disabled={loading || !state?.agreed_date}
                  >
                    {state?.agreed_date
                      ? `Current: ${state.agreed_date} — change date`
                      : 'Agree a date first'}
                  </button>
                </div>
              </Panel>

              {/* Receipt */}
              <Panel id="panel-receipt" title="Booking receipt">
                {state?.booked?.flight ? (
                  <div className="space-y-3">
                    <div className="flex items-center justify-between">
                      <Pill tone="ok">Confirmed</Pill>
                      <span className="font-pixel text-[9px] text-ink3/50">
                        {state.booked.booked_at || new Date().toLocaleDateString()}
                      </span>
                    </div>

                    {/* Booking reference */}
                    <div className="rounded border border-ok/20 bg-ok/5 px-3 py-2">
                      <div className="font-pixel text-[9px] text-ok/60 uppercase tracking-wider">Booking reference</div>
                      <div className="font-pixel text-[16px] font-bold text-ok tracking-widest">
                        {state.booked.ref || `PNR-${Math.random().toString(36).slice(2, 8).toUpperCase()}`}
                      </div>
                    </div>

                    {/* Destination */}
                    <div className="font-pixel text-[14px] font-bold text-ink">
                      {state.booked.flight}
                    </div>
                    {state.booked.destination_id && (
                      <div className="font-pixel text-[10px] text-ink3">
                        {state.booked.destination_id}
                      </div>
                    )}

                    {/* Explorer route stopover badge */}
                    {state.booked.stopover && (
                      <div className="inline-flex items-center gap-1 rounded-full border border-brand-400/40 bg-brand-400/10 px-2 py-0.5 font-pixel text-[10px] text-brand-400">
                        <span>Via {state.booked.stopover.name} ({state.booked.stopover.city_id})</span>
                      </div>
                    )}

                    {/* Outbound leg */}
                    {state.booked.outbound && (
                      <FlightLeg label="OUTBOUND" tone="text-ok" leg={state.booked.outbound} />
                    )}

                    {/* Inbound leg */}
                    {state.booked.inbound && (
                      <FlightLeg label="RETURN" tone="text-brand-400" leg={state.booked.inbound} />
                    )}

                    {/* Pricing breakdown */}
                    <div className="hud-divider" />
                    <div className="space-y-1 font-pixel text-[11px]">
                      {state.booked.per_person != null && (
                        <div className="flex justify-between text-ink2">
                          <span>Per person</span>
                          <span className="text-warn">
                            ${Number(state.booked.per_person).toFixed(2)}
                          </span>
                        </div>
                      )}
                      {state.booked.total != null && (
                        <div className="flex justify-between text-ink">
                          <span className="font-bold">Group total</span>
                          <span className="font-bold text-warn">
                            ${Number(state.booked.total).toFixed(2)}
                          </span>
                        </div>
                      )}
                      {state.booked.seats != null && (
                        <div className="flex justify-between text-ink3">
                          <span>Seats</span>
                          <span>{state.booked.seats}</span>
                        </div>
                      )}
                      {state.booked.carriers?.length > 0 && (
                        <div className="flex justify-between text-ink3">
                          <span>Carriers</span>
                          <span>{state.booked.carriers.join(', ')}</span>
                        </div>
                      )}
                      {state.booked.cost_ref && (
                        <div className="flex justify-between text-ink3/50">
                          <span>cost_ref</span>
                          <span>{state.booked.cost_ref}</span>
                        </div>
                      )}
                    </div>
                  </div>
                ) : (
                  <p className="text-sm text-ink3">
                    Select a flight and confirm to see your booking receipt.
                  </p>
                )}
              </Panel>
            </div>
          </div>
        </main>
      </div>

      {/* ── modals + toast ──────────────────────────────────────── */}
      <AddAgentModal
        open={addOpen}
        onClose={() => setAddOpen(false)}
        onSubmit={(data) => run(() => api.addMember(data), `${data.name} checked in`)}
      />

      {state?.synthesis && !state?.decision && (
        <DecisionModal
          state={state}
          onDecide={(opt) => run(() => api.decide(opt), `${opt} chosen`)}
          onConfirm={handleConfirmDirect}
        />
      )}

      <Toast toast={toast} onDone={() => setToast(null)} />

      {calOpen && (
        <DepartureCalendar
          agreedDate={state?.agreed_date}
          onSelect={handleChangeDeparture}
          onClose={() => setCalOpen(false)}
        />
      )}

      {changeModal && prevBooked && (
        <ItineraryChangeModal
          oldBooked={prevBooked}
          newCards={state?.cards || []}
          origin={state?.origin}
          onProceed={handleProceedRebook}
          onClose={() => { setChangeModal(false); setPrevBooked(null) }}
        />
      )}
    </div>
  )
}
