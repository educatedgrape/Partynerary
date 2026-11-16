'use client'

/**
 * The side-profile cutaway: a terminal lounge seen edge-on, with the aircraft
 * waiting at the gate on the right.
 *
 * Layout is absolute positioning against a fixed floor line. Agents stand ON
 * that line; the plane sits beyond it. Everything is composed left-to-right so
 * a spawning agent can walk in from off-screen and the boarding animation can
 * carry them out to the right.
 */
import { useEffect, useRef, useState } from 'react'
import PixelChar from './PixelChar'

const FLOOR = 74 // px from the bottom of the stage to the lounge floor

/* ------------------------------------------------------------------ plane */
// The source art arrived as a 640x640 GIF with an opaque white ground, which
// would have rendered as a white square on the night apron. `key_plane.py`
// keyed the pure-white background to alpha, cropped to the aircraft, and threw
// away the drop shadow - which was invisible on a dark ground but occupied the
// bottom quarter of the frame and left the aeroplane hovering. What is imported
// here is 507x175 of aircraft and nothing else, so the image bottom IS the
// undercarriage and no offset has to compensate for empty pixels.
//
// The fuselage survived the key because it is #F6F6F5 - a few levels off the
// pure white it sat on.
//
// Movement is CSS, not frames: a 9KB still that sways beats a 632KB animation.
const PLANE_W = 300               // rendered width; 507x175 -> 300x104

function Aircraft({ status }) {
  // status: 'waiting' | 'ready' | 'boarded'
  const ready = status === 'ready'
  const boarded = status === 'boarded'
  return (
    <div
      className={`absolute right-4 ${boarded ? 'animate-boardOff' : 'animate-taxi'}
                  motion-reduce:animate-none`}
      style={{ bottom: FLOOR - 4 }}
    >
      {/* Boarding glow, pooled on the apron under the aircraft. The old hull was
          drawn, so "ready" could light its own cabin windows; fixed artwork
          cannot change, so the state is told by the light around it instead. */}
      {ready && (
        <div className="pointer-events-none absolute -bottom-1 left-1/2 h-5 w-60
                        -translate-x-1/2 rounded-[50%] bg-accent-600/25 blur-md
                        animate-pulseSoft motion-reduce:animate-none" />
      )}
      <img
        src="/plane-still.png"
        alt=""
        aria-hidden="true"
        draggable="false"
        className="relative block"
        style={{
          width: PLANE_W,
          height: 'auto',
          maxWidth: 'none',
          // drop-shadow follows the artwork's own alpha, so the contact shadow
          // traces the aircraft instead of boxing it. It also replaces the
          // baked-in shadow that was cropped out of the asset.
          filter: ready
            ? 'drop-shadow(0 0 16px rgba(34,211,238,0.45)) drop-shadow(0 10px 8px rgba(0,0,0,0.55))'
            : 'drop-shadow(0 10px 8px rgba(0,0,0,0.55))',
        }}
      />
    </div>
  )
}

/* -------------------------------------------------------------- furniture */
function Bench({ left }) {
  return (
    <div className="absolute" style={{ left, bottom: FLOOR - 2 }}>
      <svg width="150" height="42" viewBox="0 0 150 42" className="pixelated">
        <rect x="0" y="14" width="150" height="7" fill="#2c363d" />
        <rect x="0" y="12" width="150" height="3" fill="#38bdf8" opacity="0.55" />
        <rect x="6" y="21" width="6" height="20" fill="#232b31" />
        <rect x="138" y="21" width="6" height="20" fill="#232b31" />
        <rect x="70" y="21" width="6" height="20" fill="#232b31" />
      </svg>
    </div>
  )
}

function DepartureBoard({ booked, agreedDate, status, selectedCard }) {
  // After booking: pull destination, date, flight numbers from booked state.
  // When a card is selected (pre-booking): show its flight code.
  // Otherwise: show agreed date only; destination stays placeholder.
  const fmtDate = (d) => {
    if (!d) return '--/--'
    if (/^\d{8}$/.test(d)) {
      const months = ['JAN','FEB','MAR','APR','MAY','JUN',
                      'JUL','AUG','SEP','OCT','NOV','DEC']
      return `${d.slice(6, 8)} ${months[parseInt(d.slice(4, 6)) - 1] || '---'}`
    }
    return d
  }
  // Determine display data: booked > selected card > placeholder
  const card = selectedCard
  const dest = booked?.flight || card?.destination || card?.destination_id || null
  const date = booked?.outbound?.date || card?.outbound?.date || agreedDate
  const fnums = booked?.outbound?.flight_numbers || card?.outbound?.flight_numbers || []
  const airlines = booked?.carriers || card?.carriers || []
  // Primary flight code for the departure board headline
  const flightCode = fnums.length > 0 ? fnums[0] : null
  return (
    <div className="absolute left-6 top-6 w-56 overflow-hidden rounded
                    border border-brand-500/30 bg-black/70 p-2 font-pixel
                    text-[10px] shadow-[0_0_24px_-6px_rgba(56,189,248,0.5)]
                    backdrop-blur-sm">
      {/* Signage, not a panel: a lit board bolted to the wall of the lounge.
          It keeps its own frame so it never reads as another HUD card. */}
      <div className="mb-1 flex items-center justify-between tracking-[0.1em]
                      text-brand-400">
        <span>DEPARTURES</span>
        <span className="h-1.5 w-1.5 rounded-full bg-ok animate-pulseSoft" />
      </div>
      <div className="mb-1.5 h-px w-full bg-brand-500/40" />
      <div className="flex justify-between text-ink">
        <span>{flightCode || dest || '\u2014 \u2014 \u2014'}</span>
        <span className="text-brand-400">{fmtDate(date)}</span>
      </div>
      {dest && flightCode && (
        <div className="mt-0.5 text-[9px] text-ink2 truncate">
          {dest}
        </div>
      )}
      {fnums.length > 0 && (
        <div className="mt-0.5 text-[9px] text-ink2">
          {fnums.join(' + ')}
        </div>
      )}
      {airlines.length > 0 && (
        <div className="mt-0.5 text-[9px] text-ink3/60 truncate">
          {airlines.join(' · ')}
        </div>
      )}
      <div className="mt-1 text-[9px] uppercase tracking-widest text-warn">
        {status}
      </div>
    </div>
  )
}

/* ------------------------------------------------------------- speech bubble */
function Bubble({ text, amount, tone, costRef, unresolved, voiced }) {
  const toneClass =
    tone === 'veto' || tone === 'withdraw' ? 'bubble-veto'
      : tone === 'accept' ? 'bubble-accept' : ''
  // The badge shows the value of ONE Atlas field, which is rarely the same
  // number as a per-person trip total. Naming the field stops the two reading
  // as a contradiction when they are simply different quantities.
  const field = costRef ? costRef.split('.').pop() : null
  return (
    <div className={`bubble ${toneClass} animate-bubblePop max-w-[210px]`}>
      {text}
      {/* Which lines a model wrote is visible on camera. The words may be
          generated; the move and the figure beside it never are. */}
      {voiced && (
        <span className="ml-1 rounded-sm border border-current px-1 text-[9px] opacity-60"
              title="wording by the member's voice model">
          voiced
        </span>
      )}
      {unresolved && (
        <span className="ml-1 rounded-sm bg-danger px-1 text-canvas">
          unresolved ref
        </span>
      )}
      {amount != null && (
        <span className="ml-1 whitespace-nowrap rounded-sm bg-warn px-1 text-canvas">
          {Number(amount).toFixed(2)}
          {field && <span className="ml-1 opacity-70">{field}</span>}
        </span>
      )}
    </div>
  )
}

/* -------------------------------------------------------------------- agent */
function Agent({ member, index, total, bubble, vetoed, spawnedAt }) {
  const [frame, setFrame] = useState(0)
  const [arrived, setArrived] = useState(false)
  const walkedIn = useRef(false)

  // Walk in once, then settle. Re-running this on every render would leave
  // agents permanently jogging on the spot.
  useEffect(() => {
    if (walkedIn.current) return
    walkedIn.current = true
    const t = setTimeout(() => setArrived(true), 900)
    return () => clearTimeout(t)
  }, [spawnedAt])

  useEffect(() => {
    const id = setInterval(() => setFrame((f) => f + 1), 210)
    return () => clearInterval(id)
  }, [])

  // Spread agents across the lounge floor, keeping clear of the aircraft.
  // The plane occupies ~300px from the right, so agents stay in the left 55%.
  const maxSpread = 260 // px range for agents, well clear of the plane
  const slot = total <= 1 ? 0.3 : (index / Math.max(1, total - 1)) * 0.85
  const left = 100 + slot * maxSpread

  const seated = arrived && index % 2 === 1

  return (
    <div
      className={`absolute flex flex-col items-center ${arrived ? '' : 'animate-walkIn'}`}
      style={{ left, bottom: seated ? FLOOR + 6 : FLOOR, zIndex: 20 + index }}
    >
      {bubble && (
        <div className="mb-2">
          <Bubble text={bubble.text} amount={bubble.amount} tone={bubble.move}
                  costRef={bubble.cost_ref} unresolved={bubble.unresolved}
                  voiced={bubble.voiced} />
        </div>
      )}
      <PixelChar
        name={member.member}
        pose={!arrived ? 'walk' : seated ? 'sit' : 'idle'}
        frame={frame}
        facing={1}
        scale={0.62}
        vetoed={vetoed}
      />
      <div className="mt-0.5 rounded border border-glass-edge bg-black/60 px-1.5
                      font-pixel text-[9px] tracking-wide backdrop-blur-sm">
        <span className="text-ink">{member.member}</span>
        <span className="ml-1 text-brand-400">{member.ceiling}</span>
      </div>
    </div>
  )
}

/* ------------------------------------------------------------------- stage */
export default function Terminal({ state, bubbleFor, planeStatus, selectedCard }) {
  const members = state.members || []
  return (
    <div className="hud relative h-[420px] overflow-hidden">
      {/* night beyond the glass */}
      <div className="absolute inset-0 bg-gradient-to-b from-[#0a1119]
                      via-[#0f1720] to-[#141b21]" />
      {/* apron floodlight low on the horizon */}
      <div className="pointer-events-none absolute right-24 top-16 h-40 w-56
                      rounded-full bg-sun opacity-[0.14] blur-3xl" />
      {/* window mullions - the wall is glass, so the frames are what you see */}
      <div className="absolute inset-x-0 top-10 h-32">
        <div className="flex h-full gap-3 px-4">
          {Array.from({ length: 12 }).map((_, i) => (
            <div key={i} className="flex-1 rounded-sm border border-white/10
                                    bg-gradient-to-b from-brand-500/[0.07]
                                    to-transparent" />
          ))}
        </div>
      </div>
      {/* cloud drifting past the glass, backlit rather than white */}
      <div className="pointer-events-none absolute left-0 top-16 flex gap-24
                      opacity-40 animate-drift">
        {Array.from({ length: 6 }).map((_, i) => (
          <div key={i} className="h-6 w-24 rounded-full bg-white/10 blur-[3px]" />
        ))}
      </div>

      <DepartureBoard
        booked={state.booked}
        agreedDate={state.agreed_date}
        status={planeStatus === 'boarded' ? 'BOARDED'
          : planeStatus === 'ready' ? 'NOW BOARDING' : 'AWAITING CONSENSUS'}
        selectedCard={selectedCard}
      />

      {/* floor */}
      <div className="absolute inset-x-0 border-t border-glass-edge bg-floor"
           style={{ bottom: 0, height: FLOOR }} />
      {/* tile seams - what makes the floor read as receding rather than flat */}
      <div className="absolute inset-x-0 flex" style={{ bottom: 0, height: FLOOR }}>
        {Array.from({ length: 22 }).map((_, i) => (
          <div key={i} className="h-full flex-1 border-r border-white/[0.04]" />
        ))}
      </div>
      <div className="absolute inset-x-0" style={{ bottom: FLOOR - 2 }}>
        <div className="h-0.5 w-full bg-brand-500
                        shadow-[0_0_12px_rgba(56,189,248,0.55)]" />
      </div>

      <Bench left={140} />
      <Bench left={330} />

      <Aircraft status={planeStatus} />

      {members.map((m, i) => (
        <Agent
          key={m.member}
          member={m}
          index={i}
          total={members.length}
          bubble={bubbleFor(m.member)}
          vetoed={(state.impact?.breached || []).includes(m.member)}
          spawnedAt={m.member}
        />
      ))}

      {members.length === 0 && (
        <div className="absolute inset-0 flex items-center justify-center">
          <div className="hud-strong px-6 py-4 text-center">
            <div className="hud-label mb-1">Terminal empty</div>
            <p className="text-sm text-ink2">
              No agents yet. Create one and they will walk in.
            </p>
          </div>
        </div>
      )}
    </div>
  )
}
